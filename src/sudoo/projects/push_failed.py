"""E-mail the person whose push could not be deployed.

When a build started by a push (an automatic update, a staging rebuild on push: projects/builds.py, trigger "push")
fails, the person who pushed gets an e-mail (template "push_failed", or "push_failed_ar" in Arabic): the project, the
branch, the commit, what failed in plain words, the error details of "Why it failed" (core/failure.py, already
cleaned) and what to do. For a repository the panel cannot read (a new private submodule, the main repository) it
says how to give access: the panel's GitHub App first, otherwise a read-only deploy key, with a link to the branch.

Who pushed: the GitHub push payload's pusher (or its head commit's committer or author, triggers.pusher_of); with
polling, the author of the commit fetched (the local clone). GitHub's noreply addresses are skipped. Without a usable
address nobody is e-mailed: the project's notification channels already receive "build_failed"
(notifications/), and the build's history entry says that nobody could be e-mailed.

At most one e-mail per branch and commit (collection "push_failure_emails"); the project option "email_pusher"
(PROJECT_EMAIL_PUSHER, on by default) turns it off. Each e-mail sent is recorded in the audit log with its recipient,
never its text. The language is the matching panel account's, else the panel's default language.
"""

from __future__ import annotations

import time
from typing import Any

from sudoo import projects
from sudoo.config import settings
from sudoo.core import audit
from sudoo.core.jobs import Job
from sudoo.core.shell import capture
from sudoo.instances import store
from sudoo.repos.gitrepo import git
from sudoo.storage import storage

COLLECTION = "push_failure_emails"
NOREPLY = "@users.noreply.github.com"

WHAT_TO_DO = {
    "repository": (
        "The panel cannot read the repository {repository}. Give the panel's GitHub App access to it (recommended: "
        "GitHub, the App's installation, Repository access), or add a read-only deploy key for it (the branch page, "
        "Why it failed). Then retry the update from the branch page."
    ),
    "requirements": (
        "A Python requirement of the modules could not be installed. Check the package name and version in "
        "requirements.txt, push a fix, and the branch is deployed again."
    ),
    "tests": "Fix the failing tests and push again: the branch is built again with your next commit.",
    "odoo": (
        "Odoo could not install or upgrade the modules. The error details show the module and the file; fix it and "
        "push again."
    ),
    "git": "Git could not bring the new code. The error details say why; fix it on GitHub and push again.",
    "other": "Push a fix, or ask your administrator: the server's log has the full details.",
}
WHAT_TO_DO_AR = {
    "repository": (
        "لا تستطيع اللوحة قراءة المستودع {repository}. امنح تطبيق GitHub الخاص باللوحة صلاحية الوصول إليه (الطريقة "
        "الموصى بها: في GitHub، إعدادات تثبيت التطبيق، Repository access)، أو أضف مفتاح نشر للقراءة فقط له (صفحة "
        "الفرع، سبب الفشل). ثم أعد محاولة التحديث من صفحة الفرع."
    ),
    "requirements": (
        "تعذّر تثبيت أحد متطلبات Python للوحدات. تحقّق من اسم الحزمة وإصدارها في requirements.txt، وادفع إصلاحًا "
        "ليُنشر الفرع من جديد."
    ),
    "tests": "أصلح الاختبارات الفاشلة وادفع من جديد: يُبنى الفرع مرة أخرى مع إيداعك التالي.",
    "odoo": "تعذّر على Odoo تثبيت الوحدات أو ترقيتها. تُظهر تفاصيل الخطأ الوحدة والملف؛ أصلحه وادفع من جديد.",
    "git": "تعذّر على Git جلب الشيفرة الجديدة. توضّح تفاصيل الخطأ السبب؛ أصلحه في GitHub وادفع من جديد.",
    "other": "ادفع إصلاحًا، أو اسأل مسؤول اللوحة: في سجل الخادم التفاصيل كاملة.",
}


def usable(email: str) -> bool:
    email = email.strip().lower()
    return "@" in email and not email.endswith(NOREPLY) and " " not in email


def recipient(job: Job, commit: dict[str, Any] | None) -> dict[str, str] | None:
    """{"name", "email"} of who pushed (job.details["pusher"]), else the fetched commit's author; None when there is
    no usable address."""
    pusher = job.details.get("pusher") or {}
    if usable(str(pusher.get("email") or "")):
        return {"name": str(pusher.get("name") or ""), "email": str(pusher["email"]).strip()}
    sha = str((commit or {}).get("full_sha") or "")
    if sha and store.exists(job.instance):
        found = capture(git(store.addons_dir(job.instance), "log", "-1", "--format=%an%x1f%ae", sha, "--"))
        name, _, email = found.partition("\x1f")
        if usable(email):
            return {"name": name, "email": email.strip()}
    return None


def _language(email: str) -> str:
    from sudoo import accounts

    try:
        name = accounts.find(email)
        account = accounts.get(name) if name else None
    except Exception:  # noqa: BLE001 - the panel's default language then
        account = None
    return accounts.language(account) if account else settings.default_language


def branch_link(project_id: str, branch: str) -> str:
    from urllib.parse import quote

    from sudoo.mail.render import panel_url

    base = (panel_url() or "").rstrip("/")
    return f"{base}/projects/{quote(project_id, safe='')}/branches/{quote(branch, safe='')}/history" if base else ""


def notify(
    job: Job, project_id: str, branch: str, commit: dict[str, Any] | None, failure: dict[str, Any]
) -> dict[str, Any]:
    """E-mail who pushed about the failed build, once per branch and commit. Returns what the history entry keeps:
    {"emailed": True, "at"} or {"emailed": False, "reason": "option_off" | "no_address" | "already" | "mail_failed"}."""
    project = projects.get(project_id)
    if project is None:
        return {"emailed": False, "reason": "no_project"}
    if not projects.option(project, "email_pusher"):
        return {"emailed": False, "reason": "option_off"}
    sha = str((commit or {}).get("full_sha") or (commit or {}).get("sha") or "")
    key = f"{project_id}:{branch}:{sha or job.id}"
    if storage().get(COLLECTION, key) is not None:
        return {"emailed": False, "reason": "already"}
    person = recipient(job, commit)
    if person is None:
        storage().put(COLLECTION, key, {"at": time.time(), "to": None})
        return {"emailed": False, "reason": "no_address"}
    arabic = _language(person["email"]) == "ar"
    kind = str(failure.get("kind") or "other")
    todo = (WHAT_TO_DO_AR if arabic else WHAT_TO_DO).get(kind, (WHAT_TO_DO_AR if arabic else WHAT_TO_DO)["other"])
    rule = failure.get("rule")
    if isinstance(rule, dict) and not arabic:  # the rules' text is English; an Arabic mail keeps its own text
        todo = f"{rule['title']}. {rule['fix']} " + todo
    from sudoo import mail
    from sudoo.mail.catalog import details_block, lines_block

    project_name = str(project.get("name") or project_id)
    shown_commit = f"{(commit or {}).get('sha') or sha[:7]} {(commit or {}).get('subject') or ''}".strip()
    step = str(failure.get("step") or "")
    labels = ("المشروع", "الفرع", "الإيداع", "الخطوة") if arabic else ("Project", "Branch", "Commit", "Step")
    values = {
        "name": person["name"] or person["email"].split("@")[0],
        "project": project_name,
        "branch": branch,
        "commit": shown_commit,
        "step": step,
        "reason": str(failure.get("reason") or ""),
        "facts": details_block(list(zip(labels, (project_name, branch, shown_commit, step), strict=True))),
        "details": lines_block([str(line) for line in failure.get("details") or []]),
        "what_to_do": todo.format(repository=failure.get("repository") or "?"),
        "link": branch_link(project_id, branch),
    }
    try:
        mail.send(person["email"], "push_failed_ar" if arabic else "push_failed", **values)
    except Exception as error:  # noqa: BLE001 - a mail server that refuses never changes the build
        job.write(f"Could not e-mail {person['email']} about the failed build: {error!r}\n")
        return {"emailed": False, "reason": "mail_failed"}
    storage().put(COLLECTION, key, {"at": time.time(), "to": person["email"]})
    audit.record(
        "system",
        None,
        "pusher e-mailed about a failed deployment",
        job.instance,
        detail=f"{project_id} {branch} {sha[:12]}: to {person['email']}",
    )
    return {"emailed": True, "at": time.time()}
